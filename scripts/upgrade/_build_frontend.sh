#!/bin/bash
# ============================================================================
# scripts/upgrade/_build_frontend.sh — build the Angular FE and stamp
# provenance (cn 0472b31f trap family; 2026-10-04)
# ============================================================================
#
# THE TRAP. The stage.sh staleness trap family has materialized TWICE on
# dist/ensemble-prod and once on frontend/dist (2026-10-02: 21 files would
# have shipped v0.16.9-era under v0.16.10 label). The structural fix is
# provenance verification at stage time, which requires a provenance
# sidecar to exist next to the build. This script IS the wrapper that
# produces that sidecar for the FE: it runs `npm run build` in
# frontend/, then writes frontend/dist/.build-provenance.json (the same
# JSON the stage verifier reads — sibling to the file the verifier
# points at, which is the FE index.html at
# frontend/dist/frontend/browser/index.html).
#
# USAGE:
#   bash scripts/upgrade/_build_frontend.sh
#
# NO argv. NO env override. The script is a single-purpose operator
# gesture: build FE + write provenance, atomically. It exits 0 on
# success, 1 on build failure, 78 on env refusal. The build is the
# SAME `npm run build` the project has always used; the provenance
# write is the new piece.
#
# This script is a HELPER, not a gate — it does not refuse stale state
# (that's stage.sh's job). The operator runs it after every meaningful
# frontend/ change; stage.sh verifies the sidecar exists + matches the
# current tree at stage time.
#
# BSD/macOS safety: git-native primitives only (git rev-parse, git
# status --porcelain) — no stat/date/sha-tool gymnastics. This matches
# the lib.sh portability discipline.
# ============================================================================

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

LOG_TAG="upgrade-build-fe"

# shellcheck source=scripts/upgrade/lib.sh
. "$SCRIPT_DIR/lib.sh"

# Initialize UP_TARGET so the lib.sh _log/_warn helpers have a target
# label. This is a build helper, not a stage; we pick a stable label
# ("fe") so the journal history (if any) is greppable.
UP_TARGET="${UP_TARGET:-fe}"
export UP_TARGET

# Find the FE root. The convention: <REPO_ROOT>/frontend (the only
# supported layout — the spec, package.json, and angular.json all live
# there). Refuse if missing.
FE_ROOT="$REPO_ROOT/frontend"
if [ ! -d "$FE_ROOT" ]; then
    _warn "no frontend/ directory at $FE_ROOT — refusing (this script is a build helper, not a stage; the operator must be in the agents-ensemble checkout)"
    exit 78
fi

# Verify node/npm is on PATH (we cannot rely on a hard path the way
# stage.sh does for `uv`, because the FE build's toolchain is per-host;
# the operator's session is the right place to fix their PATH). HARD
# refuse with remedy text if npm is missing.
if ! command -v npm >/dev/null 2>&1; then
    _warn "npm not found on PATH — refusing to build. Remedies, in order:"
    _warn "  1) install Node + npm per https://nodejs.org/ (or your platform's package manager)"
    _warn "  2) source your shell rc to put npm on PATH (bash: 'source ~/.bashrc'; zsh: 'source ~/.zshrc')"
    _warn "  3) invoke this script from a session that already has npm on PATH"
    exit 78
fi

# Compute the staging-tree identity ONCE (used in the provenance
# sidecar). HEAD is the commit at the time of the build (NOT the
# artifact's modification time — we want git identity, not fs mtime).
# The dirty flag is the WORKING-TREE state at the moment of the build.
HEAD_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || true)"
GIT_DIRTY="$(_git_dirty_porcelain)"

# Run the build. Bare `npm run build` (the package.json shape) — no
# scope creep. The build target's output is at frontend/dist/frontend/
# browser/ (Angular's default for the production config).
_log "running 'npm run build' in $FE_ROOT (head=${HEAD_SHA:0:12} dirty=$GIT_DIRTY)"
if ! (cd "$FE_ROOT" && npm run build); then
    _warn "FE build FAILED (npm run build exit non-zero) — refusing to write a provenance sidecar for a failed build"
    exit 1
fi

# Sanity: the build must have produced the canonical entry point. The
# stage verifier points at frontend/dist/frontend/browser/index.html
# (the file the verifier hashes). If the build did not produce it, we
# cannot write a sidecar the stage verifier will accept — refuse loud.
FE_BROWSER_DIR="$FE_ROOT/dist/frontend/browser"
if [ ! -d "$FE_BROWSER_DIR" ] || [ ! -f "$FE_BROWSER_DIR/index.html" ]; then
    _warn "FE build produced no $FE_BROWSER_DIR/index.html — refusing to write provenance (the stage verifier hashes this file; a missing sidecar target means the sidecar would be lying about a build that didn't happen)"
    exit 1
fi

# Write provenance sidecar. The verifier points at the index.html
# (sibling to the sidecar — by convention, "<artifact>.build-provenance
# .json"). Same JSON shape as the backend provenance; the build_tool
# field distinguishes them for log greppability.
FE_TARGET="$FE_BROWSER_DIR/index.html"
_provenance_write "$FE_TARGET" "$HEAD_SHA" "$GIT_DIRTY" "npm run build:frontend" \
    || { _warn "provenance write FAILED for $FE_TARGET — the build succeeded but the sidecar is missing; stage will refuse as provenance-missing. Re-run this script to retry the sidecar write."; exit 1; }

_log "FE build + provenance complete (sidecar: $FE_TARGET.build-provenance.json)"
exit 0
