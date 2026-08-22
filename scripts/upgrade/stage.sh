#!/bin/bash
# ============================================================================
# scripts/upgrade/stage.sh — build + assemble a release (P2.1 T2, ADR-004/009)
# ============================================================================
# Assembles $INSTALL_DIR/releases/<VERSION>/ — the staged payload (D-FA4.1):
#
#   releases/<ver>/ensemble-prod        (bare PyInstaller build, or
#                                        --skip-build <path> prebuilt binary)
#   releases/<ver>/agents/              (repo agents/ tree, clean copy)
#   releases/<ver>/frontend/dist/       (repo frontend build, clean copy)
#   releases/<ver>/launcher.sh          [ARCHITECT AMENDMENT 2026-08-22 — the
#                                        launcher joins the staged payload; the
#                                        manifest gains launcher_sha256; promote
#                                        swaps it in the stopped window]
#   releases/<ver>/config.yaml
#   releases/<ver>/manifest.json        (ADR-004 M5 fields + D-FA4.4 checksums)
#
# NO `.env` EVER inside the release dir (ADR-014/m6) — the env marker
# ENSEMBLE_SELF_ENV=<dev|demo|live|sandbox> is staged into INSTALL_DIR/.env
# (D-FA2.3 RATIFIED — P2.2's env self-match consumes this marker).
#
# NO FLIP: staging never touches `current` (that is promote.sh).
#
# VERSION discipline (ADR-009 D3): explicit VERSION required; HEAD must be
# exactly tagged VERSION (git describe --tags --exact-match) else exit 78 —
# no auto pull, no network fetch ANYWHERE in this pipeline (local checkout
# only). NEVER `make build`/`make pyinstaller` from here: the ensure-latest
# chain would yank the feature branch (deploy.sh:19-22 rationale).
#
# USAGE:
#   VERSION=v0.10.6 bash scripts/upgrade/stage.sh demo
#   bash scripts/upgrade/stage.sh demo --version v0.10.6
#   bash scripts/upgrade/stage.sh sandbox --version v1 --skip-build ./stub-prod
#   (sandbox also needs INSTALL_DIR=<dir> PORT=<port> [POSTGRES_DB=<db>])
#
# ROLLBACK SAFETY DERIVATION (D-FA4.5): `rollback_safe` defaults to the
# release author's call via ENSEMBLE_ROLLBACK_SAFE={0,1}; when unset it is
# DERIVED — false iff the staged migration set contains destructive DDL
# (DROP TABLE / DROP COLUMN → contains_contract_phase=true), else true.
# known_schema_gen = migration head at stage time (informational only).
#
# EXIT CODES: 0 staged (or idempotently re-staged) · 1 stage failure ·
# 78 config refuse (missing VERSION / tag mismatch / missing binary / unknown
# target / unconfirmed live / pipeline-busy).
# ============================================================================

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_TAG="upgrade-stage"

VERSION="${VERSION:-}"
SKIP_BUILD=0
BINARY_SRC=""
TARGET_ARG=""
args=("$@")
i=0
while [ $i -lt ${#args[@]} ]; do
    arg="${args[$i]}"
    case "$arg" in
        demo|live|sandbox) TARGET_ARG="$arg" ;;
        --version)
            i=$((i + 1))
            VERSION="${args[$i]:-}"
            ;;
        --skip-build)
            SKIP_BUILD=1
            # optional following arg = prebuilt binary path
            nxt=$((i + 1))
            if [ $nxt -lt ${#args[@]} ]; then
                case "${args[$nxt]}" in
                    -*) ;;
                    "") ;;
                    *) BINARY_SRC="${args[$nxt]}"; i=$nxt ;;
                esac
            fi
            ;;
        -h|--help) sed -n '2,44p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "stage: unknown flag '$arg' — see --help" >&2; exit 78 ;;
    esac
    i=$((i + 1))
done

# shellcheck source=scripts/upgrade/lib.sh
. "$SCRIPT_DIR/lib.sh"

resolve_env "${TARGET_ARG:-${TARGET:-demo}}"
require_live_guard "$UP_TARGET"
echo_env_triple

# ── VERSION discipline (ADR-009 D3) ─────────────────────────────────────────
if [ -z "$VERSION" ]; then
    _warn "explicit VERSION required — e.g. VERSION=v0.10.6 bash scripts/upgrade/stage.sh demo (no default, no auto pull)"
    exit 78
fi
case "$VERSION" in
    */*|.*|*" "*) _warn "invalid VERSION '$VERSION' (must be a plain version token)"; exit 78 ;;
esac

GIT_DESCRIBE="$(git -C "$REPO_ROOT" describe --tags --exact-match HEAD 2>/dev/null)" || GIT_DESCRIBE=""
if [ "$GIT_DESCRIBE" != "$VERSION" ]; then
    _warn "VERSION '$VERSION' does not match the exact tag at HEAD (git describe: '${GIT_DESCRIBE:-<untagged>}') — refusing (ADR-009 D3: stage what was built, no auto pull / no network fetch)"
    exit 78
fi

# ── Build (bare PyInstaller — deploy.sh:195-197 pattern) ────────────────────
BINARY="$REPO_ROOT/dist/ensemble-prod"
if [ "$SKIP_BUILD" = "0" ]; then
    NEED_BUILD=0
    if [ ! -x "$BINARY" ]; then
        NEED_BUILD=1
        _log "no binary at $BINARY — building"
    fi
    if [ "$NEED_BUILD" = "1" ]; then
        _log "PyInstaller build (bare, branch-safe — NEVER make build/pyinstaller: ensure-latest would yank the branch)"
        if ! (cd "$REPO_ROOT" && rm -rf build/ && uv run python -m PyInstaller ensemble.spec); then
            _warn "PyInstaller build FAILED"
            exit 1
        fi
        [ -x "$BINARY" ] || { _warn "build produced no $BINARY"; exit 78; }
    else
        _log "using existing binary at $BINARY (pass --skip-build to force-bypass a rebuild check, or rm dist/)"
    fi
    BINARY_SRC="$BINARY"
else
    if [ -z "$BINARY_SRC" ]; then
        BINARY_SRC="$BINARY"
    fi
    if [ ! -f "$BINARY_SRC" ]; then
        _warn "--skip-build: binary '$BINARY_SRC' not found"
        exit 78
    fi
    _log "build skipped (--skip-build) — binary source: $BINARY_SRC"
fi

# ── Payload sources must exist ──────────────────────────────────────────────
for req in "$REPO_ROOT/agents" "$REPO_ROOT/config.yaml" "$REPO_ROOT/launcher.sh"; do
    if [ ! -e "$req" ]; then
        _warn "missing payload source: $req"
        exit 78
    fi
done
if [ ! -d "$REPO_ROOT/frontend/dist/frontend/browser" ]; then
    _warn "no frontend build at frontend/dist/frontend/browser — run 'cd frontend && npm run build' first (refusing to stage a UI-less release)"
    exit 78
fi

# ── Schema generation facts (manifest informational fields) ────────────────
KNOWN_SCHEMA_GEN="$(ls "$REPO_ROOT/daemon/migrations/versions" 2>/dev/null | sort | tail -1)"
[ -n "$KNOWN_SCHEMA_GEN" ] || KNOWN_SCHEMA_GEN="unknown"
if grep -liE 'DROP[[:space:]]+TABLE|DROP[[:space:]]+COLUMN' \
     "$REPO_ROOT"/daemon/migrations/versions/*.sql >/dev/null 2>&1; then
    CONTAINS_CONTRACT_PHASE=true
else
    CONTAINS_CONTRACT_PHASE=false
fi
ROLLBACK_SAFE="${ENSEMBLE_ROLLBACK_SAFE:-}"
case "$ROLLBACK_SAFE" in
    1|true)  ROLLBACK_SAFE=true ;;
    0|false) ROLLBACK_SAFE=false ;;
    "")
        # derived default (D-FA4.5): destructive migrations ⇒ unsafe to roll back
        if [ "$CONTAINS_CONTRACT_PHASE" = "true" ]; then ROLLBACK_SAFE=false; else ROLLBACK_SAFE=true; fi
        ;;
esac

# binary_version: what /livez will report (daemon __version__). Derived from
# the tag (strip leading v); ENSEMBLE_BINARY_VERSION overrides for odd cases.
BINARY_VERSION="${ENSEMBLE_BINARY_VERSION:-${VERSION#v}}"

# ── Lock (D5: stage mutates releases/ — serialize with promote/rollback) ────
if ! lock_acquire; then
    exit 78   # pipeline-busy already logged (structured, not an error)
fi
trap 'lock_release' EXIT
# stage holds this lock through LONG assembly phases (tree copies + sha256
# walks can exceed LOCK_STALE_S=300s on big trees) — heartbeat at every
# phase boundary so a concurrent promote/sweep never stale-breaks the lock
# of a LIVE owner (review m3; promote/rollback heartbeat the same way)
lock_heartbeat

# ── Install dir (demo/sandbox created on demand; live must pre-exist) ───────
if [ ! -d "$INSTALL_DIR" ]; then
    if [ "$UP_TARGET" = "live" ]; then
        _warn "live install dir $INSTALL_DIR does not exist — stage does not bootstrap live (USER-GATED migration, P2.3)"
        exit 78
    fi
    mkdir -p "$INSTALL_DIR" || { _warn "cannot create $INSTALL_DIR"; exit 1; }
fi
mkdir -p "$INSTALL_DIR/releases"

# ── Assemble into a temp dir, verify, then swap in (idempotent re-stage) ────
REL="$INSTALL_DIR/releases/$VERSION"
STAGE_TMP="$INSTALL_DIR/releases/.staging.$VERSION.$$"
rm -rf "$STAGE_TMP"
mkdir -p "$STAGE_TMP" || exit 1

_log "assembling release $VERSION → $STAGE_TMP"
cp "$BINARY_SRC" "$STAGE_TMP/ensemble-prod" || { rm -rf "$STAGE_TMP"; exit 1; }
chmod +x "$STAGE_TMP/ensemble-prod"
cp -R "$REPO_ROOT/agents" "$STAGE_TMP/agents" || { rm -rf "$STAGE_TMP"; exit 1; }
mkdir -p "$STAGE_TMP/frontend/dist/frontend"
cp -R "$REPO_ROOT/frontend/dist/frontend/browser" "$STAGE_TMP/frontend/dist/frontend/browser" || { rm -rf "$STAGE_TMP"; exit 1; }
cp "$REPO_ROOT/config.yaml" "$STAGE_TMP/config.yaml" || { rm -rf "$STAGE_TMP"; exit 1; }
cp "$REPO_ROOT/launcher.sh" "$STAGE_TMP/launcher.sh" || { rm -rf "$STAGE_TMP"; exit 1; }
chmod +x "$STAGE_TMP/launcher.sh"
lock_heartbeat   # payload copies done; checksum walks start (long phase)

# ADR-014/m6 invariant — NO .env of any kind inside a release dir (catches a
# stray repo-side .env before it can ever be staged).
if ! _no_env_in_release "$STAGE_TMP"; then
    rm -rf "$STAGE_TMP"
    exit 1
fi

# ── Manifest (ADR-004 M5 + D-FA4.4) ─────────────────────────────────────────
_log "computing manifest checksums (per-file sha256 + tree aggregates)"
BIN_SHA="$(_sha256 "$STAGE_TMP/ensemble-prod")"
LAUNCHER_SHA="$(_sha256 "$STAGE_TMP/launcher.sh")"
CONFIG_SHA="$(_sha256 "$STAGE_TMP/config.yaml")"
# one walk per tree feeds BOTH the aggregate hash and the per-file map
AGENTS_LINES="$(_tree_manifest "$STAGE_TMP/agents" "")"
FRONTEND_LINES="$(_tree_manifest "$STAGE_TMP/frontend" "")"
AGENTS_TREE="$(_tree_hash_of_lines "$AGENTS_LINES")"
FRONTEND_TREE="$(_tree_hash_of_lines "$FRONTEND_LINES")"

agents_map=""
first=1
while IFS= read -r line; do
    [ -n "$line" ] || continue
    sha="${line%%  *}"; rel="${line#*  }"
    if [ $first = 1 ]; then agents_map="\"$(_json_escape "$rel")\":\"$sha\""; first=0
    else agents_map="$agents_map, \"$(_json_escape "$rel")\":\"$sha\""; fi
done <<EOF
$AGENTS_LINES
EOF
frontend_map=""
first=1
while IFS= read -r line; do
    [ -n "$line" ] || continue
    sha="${line%%  *}"; rel="${line#*  }"
    if [ $first = 1 ]; then frontend_map="\"$(_json_escape "$rel")\":\"$sha\""; first=0
    else frontend_map="$frontend_map, \"$(_json_escape "$rel")\":\"$sha\""; fi
done <<EOF
$FRONTEND_LINES
EOF

# staged_at: STABLE across idempotent re-stages (kept from an existing
# manifest of the same version) — retention ordering must not wobble.
STAGED_AT="$(_now_iso)"
if [ -f "$REL/manifest.json" ]; then
    prev_staged="$(manifest_field "$VERSION" staged_at 2>/dev/null)"
    [ -n "$prev_staged" ] && STAGED_AT="$prev_staged"
fi

cat > "$STAGE_TMP/manifest.json" <<EOF
{
  "version": "$VERSION",
  "binary_version": "$BINARY_VERSION",
  "staged_at": "$STAGED_AT",
  "known_schema_gen": "$KNOWN_SCHEMA_GEN",
  "contains_contract_phase": $CONTAINS_CONTRACT_PHASE,
  "rollback_safe": $ROLLBACK_SAFE,
  "launcher_sha256": "$LAUNCHER_SHA",
  "binary_sha256": "$BIN_SHA",
  "config_sha256": "$CONFIG_SHA",
  "agents_tree_sha256": "$AGENTS_TREE",
  "frontend_tree_sha256": "$FRONTEND_TREE",
  "agents_manifest": {$agents_map},
  "frontend_manifest": {$frontend_map}
}
EOF

# verify the TEMP tree against the manifest we just wrote (T3: after stage).
# The temp dir lives at $INSTALL_DIR/releases/.staging.<ver>.$$ so the
# standard integrity_verify path resolution works unchanged.
lock_heartbeat   # full-tree verify walk (long phase)
if ! integrity_verify ".staging.$VERSION.$$"; then
    _warn "post-stage integrity check FAILED on the temp assembly — not swapping in"
    rm -rf "$STAGE_TMP"
    exit 1
fi

# swap in (rm + mv; no flip — `current` untouched)
rm -rf "$REL"
if ! mv "$STAGE_TMP" "$REL"; then
    _warn "failed to move staged release into place"
    rm -rf "$STAGE_TMP"
    exit 1
fi

# ── Journal init (staged mode begins) ───────────────────────────────────────
journal_init
# a re-staged version is a REBUILT artifact — a prior quarantine verdict no
# longer describes it (the operator explicitly rebuilt + re-verified it)
journal_quarantine_clear "$VERSION"

# ── ENSEMBLE_SELF_ENV marker → INSTALL_DIR/.env (D-FA2.3; NEVER in release) ─
ENV_FILE="$INSTALL_DIR/.env"
MARKER="ENSEMBLE_SELF_ENV=$UP_TARGET"
if [ -f "$ENV_FILE" ]; then
    if grep -qE '^[[:space:]]*(export[[:space:]]+)?ENSEMBLE_SELF_ENV=' "$ENV_FILE"; then
        tmp="$ENV_FILE.tmp.$$"
        sed -E "s/^[[:space:]]*(export[[:space:]]+)?ENSEMBLE_SELF_ENV=.*/$MARKER/" "$ENV_FILE" > "$tmp" \
            && mv -f "$tmp" "$ENV_FILE" || { rm -f "$tmp"; _warn "failed to update $ENV_FILE marker"; exit 1; }
    else
        tmp="$ENV_FILE.tmp.$$"
        { cat "$ENV_FILE"; printf '%s\n' "$MARKER"; } > "$tmp" \
            && mv -f "$tmp" "$ENV_FILE" || { rm -f "$tmp"; _warn "failed to append marker to $ENV_FILE"; exit 1; }
    fi
else
    {
        printf 'ENSEMBLE_SELF_ENV=%s\n' "$UP_TARGET"
        printf 'PORT=%s\n' "$PORT"
        printf 'POSTGRES_DB=%s\n' "$POSTGRES_DB"
    } > "$ENV_FILE" || { _warn "failed to create $ENV_FILE"; exit 1; }
    _log "created $ENV_FILE (self-env marker + resolved port/db; operator completes secrets per .env.prod.example)"
fi
_log "ENSEMBLE_SELF_ENV marker staged: $MARKER (in $ENV_FILE — never inside the release dir)"

# ── Final post-stage verification on the swapped-in release ─────────────────
lock_heartbeat   # second full-tree verify walk (long phase)
if ! integrity_verify "$VERSION"; then
    _warn "post-swap integrity check FAILED — release staged but DAMAGED; do not promote"
    exit 1
fi

_log "staged release $VERSION at $REL (rollback_safe=$ROLLBACK_SAFE known_schema_gen=$KNOWN_SCHEMA_GEN contains_contract_phase=$CONTAINS_CONTRACT_PHASE) — NO flip performed (promote.sh owns current)"
exit 0
