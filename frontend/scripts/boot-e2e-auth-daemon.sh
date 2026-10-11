#!/usr/bin/env bash
# Disposable daemon for the OAuth2 auth e2e suite (PLACEHOLDER — P4
# T4.7; the FULL body lands in P5 per phase4-plan T4.7 / FR-23/34
# groundwork).
#
# Pattern: frontend/scripts/boot-e2e-snapshots-daemon.sh (the in-repo
# precedent for disposable-PG daemon bootstrap: initdb -A trust,
# pg_ctl start, POSTGRES_* scrub, OPENAI_API_KEY early-exit guard,
# dual TERM/INT+EXIT trap into one cleanup(), canary wait,
# ENSEMBLE_E2E_KEEP=1 keep flag). Only the ports + data dir +
# disposable DB name + log dir + canary differ.
#
# P5 FULL BODY will add (NOT in this placeholder — deliberately
# inert until the P5 harness pairs with it):
#   1. boot the daemon with auth ON (+ the §6.3 enablement env set:
#      ENSEMBLE_AUTH_{ENABLED,CLIENT_ID,CLIENT_SECRET,ISSUER,
#      ALLOWED_ORIGINS,EXTERNAL_REDIRECT_BASE}, cookie dev flags per
#      the LAN opt-in);
#   2. mint a canary bearer via `python -m daemon.auth.cli issue-token
#      --label boot-e2e-canary --scopes "system:read,mcp:read"`;
#   3. curl the 5 public probes tokenless (/livez /readyz /api/health
#      /api/info /api/maintenance/checkpoint-cleanup/availability);
#   4. curl one bearer-gated route with the canary token;
#   5. assert the tokenless protected route 401/302s (fail-loudly).
#
# Usage (mirrors the snapshots script):
#   ENSEMBLE_E2E_KEEP=1 ./scripts/boot-e2e-auth-daemon.sh start
#   ./scripts/boot-e2e-auth-daemon.sh stop
#
# Env vars (all optional):
#   PG_PORT       — local PG port (default 15533 — distinct from the
#                   snapshots :15532 AND maintenance :15432 AND dev :5432)
#   DAEMON_PORT   — daemon port (default 18280 — distinct from the
#                   snapshots :18279 AND maintenance :8099 AND dev :8079)
#   DATA_DIR      — PG cluster data dir (default /tmp/pg_e2e_auth_$$)
#   DISPOSABLE_DB — disposable DB name (default ensemble_e2e_auth_$$)
#   OPENAI_API_KEY — must be set; the daemon's lifespan requires it
set -euo pipefail

echo "[boot-e2e-auth-daemon] PLACEHOLDER (P4 T4.7) — full body lands in P5." >&2
echo "[boot-e2e-auth-daemon] This script intentionally performs NO boot and NO network" >&2
echo "[boot-e2e-auth-daemon] action; it exists so the P5 harness has a wired filename," >&2
echo "[boot-e2e-auth-daemon] a distinct port/data-dir allocation, and the documented" >&2
echo "[boot-e2e-auth-daemon] body plan (see the header comment)." >&2
exit 0
