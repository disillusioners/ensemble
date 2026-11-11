#!/bin/bash
# Env-scrub wrapper for running ensemble-src test suites.
#
# Required pattern (per the 2 prior live-probe incidents — most recently
# 2026-09-26): POSTGRES_* / POSTGRES_URL / DATABASE_URL_POSTGRES must
# NEVER reach a test invocation. Bare ``source`` under /bin/sh silently
# no-ops the unset; this wrapper is a standalone bash process and
# echo-verifies ZERO survivors before any subprocess is spawned.
#
# Usage:  ./scripts/run_tests_scrubbed.sh <pytest-args...>
#
# Examples:
#   ./scripts/run_tests_scrubbed.sh <test-file-or-args>
#   ./scripts/run_tests_scrubbed.sh <test-file-or-args> -k <expr>
#
# Hard-coded here (NOT opt-in via env): we always scrub, never carry
# ambient POSTGRES_* through. The bash unset is built-in; the verify
# gate fails LOUD if any unset didn't take.

set -euo pipefail

# 1. Scrub every variant the project warns about, plus the bare libpq
#    connection variables. Unset is a no-op when the var is absent, so
#    this is safe to run unconditionally.
unset POSTGRES_HOST
unset POSTGRES_DB
unset POSTGRES_USER
unset POSTGRES_PASSWORD
unset POSTGRES_PORT
unset POSTGRES_URL
unset DATABASE_URL_POSTGRES
# Bare libpq connection-selection/auth vars (W2, 2026-09-28):
# ENUMERATED, deliberately NOT a `^PG` wildcard — PGDATA / PG_CONFIG-
# style vars used by tooling must not false-positive-kill the wrapper,
# while the names below are exactly the ones libpq consults when it
# builds a DEFAULT connection (the live-probe hazard: a DSN-less
# libpq/asyncpg connect silently targets whatever these say). No repo
# test legitimately consumes ambient values — the PG test harness reads
# PG_TEST_* only (tests/helpers/checkpoint_prune_pg.py:22-27).
unset PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD PGPASSFILE
unset PGSSLMODE PGSERVICE PGSERVICEFILE
# Plane workspace hygiene (2026-11, dev-lane): an unset-or-'nea' Plane
# workspace selection resolves to the PROD Plane workspace, so a test
# child armed with ambient sync config can mirror E2E projects into
# PROD. Scrub exactly the two vars the REST sync path REQUIRES for
# availability (PLANE_MCP_WORKSPACE_SLUG + PLANE_BASE_URL) — unsetting
# them structurally disables sync in test children. Plane credentials
# are deliberately NOT scrubbed: the same user-scoped key legitimately
# serves prod and e2e workspaces, so key handling is not the mechanism.
# Sync on/off flags are left alone too — workspace selection is the
# whole invariant. The leak gate below checks exactly these two names.
unset PLANE_MCP_WORKSPACE_SLUG PLANE_BASE_URL
# Bare PORT hygiene (H4, tidier round 3, 2026-11-11). Several SHELL packs
# source scripts/upgrade/launcher.sh (tests/test_release_journal.sh:1641,
# tests/test_launcher.sh:513 do `( . "$REPO_ROOT/launcher.sh"; INSTALL_DIR=…
# _journal_sweep )`). launcher.sh reads ${PORT:-} for its livez/port probes,
# so an AMBIENT PORT inherited from a daemon shell points those read-only
# probes at the LIVE daemon — the pack then passes (or fails) for the wrong
# reason. Unset it here so EVERY pack that runs through this wrapper gets the
# hygiene at once (the class, not the instances). Children that need a port
# must set their OWN fixture-scoped value.

# 2. Echo-verify ZERO survivors — FAMILY-WIDE, not enumerated (W2,
#    2026-09-28). The old loop re-listed the 7 known names, so any NEW
#    POSTGRES_* spelling (e.g. POSTGRES_FOO) leaked through the gate
#    silently. Now: ANY POSTGRES_* name still in the environment fails,
#    plus legacy DATABASE_URL_POSTGRES (not covered by the POSTGRES_
#    prefix) and the enumerated libpq set above.
#
#    Walk NAMES only (via `compgen -v`, a pure bash builtin — no GNU/
#    BSD divergence, immune to multi-line env values where continuation
#    lines previously parsed as fabricated NAME= records). `compgen -v`
#    also lists readonly/bashed internals (BASH_VERSINFO etc.), so the
#    test below filters to ONLY the target families. Names are echoed,
#    never values — a leaked var may hold credentials.
#
#    `set -u` safety: `${!v+x}` is the "exists-or-not" form; under
#    `set -u` the indirect expansion is safe because `+` does not
#    error on an unset parameter (per bash(1) §Parameter Expansion).
LEAKED=""
while IFS= read -r v; do
    case "$v" in
        POSTGRES_*)                                              LEAKED+="$v"$'\n' ;;
        DATABASE_URL_POSTGRES)                                   LEAKED+="$v"$'\n' ;;
        PGHOST|PGPORT|PGDATABASE|PGUSER|PGPASSWORD|PGPASSFILE)   LEAKED+="$v"$'\n' ;;
        PGSSLMODE|PGSERVICE|PGSERVICEFILE)                       LEAKED+="$v"$'\n' ;;
        PLANE_MCP_WORKSPACE_SLUG|PLANE_BASE_URL)                 LEAKED+="$v"$'\n' ;;
    esac
done < <(compgen -v)
# Drop any candidate that became unset between compgen -v enumeration
# and this check (defensive — nothing else in this script unsets these,
# but the brief mandates the guard).
drop=""
while IFS= read -r v; do
    [ -n "$v" ] || continue
    if [ -z "${!v+x}" ]; then
        drop+="$v"$'\n'
    fi
done < <(printf '%s\n' "$LEAKED")
if [ -n "$drop" ]; then
    while IFS= read -r v; do
        [ -n "$v" ] || continue
        LEAKED="${LEAKED//$v$'\n'/}"
    done < <(printf '%s\n' "$drop")
fi
if [ -n "$LEAKED" ]; then
    echo "ENV-SCRUB FAILURE: leaked connection vars still set:" >&2
    printf '%s\n' "$LEAKED" | sed 's/^/  /' >&2
    echo "Refusing to run tests with leaked POSTGRES_*/libpq/Plane-workspace vars." >&2
    exit 78  # EX_CONFIG — mirrors lib.sh exit-78 convention
fi

# 3. Run the venv pytest with the supplied args. The cwd must stay
#    inside the repo so pytest discovers pyproject.toml.
cd "$(dirname "$0")/.."
exec .venv/bin/pytest "$@"
