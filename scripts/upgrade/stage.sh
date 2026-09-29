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
# (D-FA2.3 RATIFIED). The marker stays the HIGHEST-PRIORITY signal — the
# tool layer reads it first (P2.2 env self-match consumes it). 2026-09-22
# supersession (user directive): the marker is OPTIONAL with explicit
# opt-out; a marker-absent daemon auto-derives the env from launch
# evidence (multi-signal — frozen-binary + releases/ for sandbox;
# install-dir + POSTGRES_DB cross-check for live/demo; dev-shape
# POSTGRES_DB for dev). Stage.sh still stages the marker so the
# operator's staging intent overrides any ambient host evidence
# (e.g. the LIVE daemon at port 9797 which pre-dates P2.1 had no
# marker; re-staging now seeds the explicit marker on a fresh promote).
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
            # optional following arg = prebuilt binary path. NEVER swallow a
            # target token (demo|live|sandbox) — `stage.sh --skip-build
            # sandbox --version v1` used to eat "sandbox" as BINARY_SRC and
            # silently default the target to demo (Batch C, council 🟢
            # suggestion taken). A leading '-' also ends the option value.
            nxt=$((i + 1))
            if [ $nxt -lt ${#args[@]} ]; then
                case "${args[$nxt]}" in
                    -*) ;;
                    "") ;;
                    demo|live|sandbox) ;;
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
        # M6 (commission v0.16.6 component 2, uv PATH hardening): `uv` is
        # NOT always on PATH — the executor session that prompted this
        # rider had no `~/.local/bin` on PATH, so `uv: command not found`
        # failed the build at stage.sh:131. Resolve via `command -v`
        # first, then fall back to the conventional pip user-install
        # location; HARD refuse (no silent degradation, no PATH mutation)
        # with remedy text if BOTH miss — the operator's session is the
        # right place to fix their PATH, never this script. WHY NOT
        # MUTATE PATH: a stage.sh PATH prepend would silently change the
        # operator's shell environment on exit (well-behaved scripts
        # don't write export-side-effects), and a shell-local export
        # would not survive the subshell `cd && rm -rf && uv run …` form
        # below.
        _uv_bin=""
        if command -v uv >/dev/null 2>&1; then
            _uv_bin="$(command -v uv)"
        elif [ -x "$HOME/.local/bin/uv" ]; then
            _uv_bin="$HOME/.local/bin/uv"
        else
            _warn "uv not found on PATH and no $HOME/.local/bin/uv — refusing to build (motivating failure: executor session PATH lacked ~/.local/bin → 'uv: command not found' at stage.sh:131 → build-failed exit 1). Remedies, in order:"
            _warn "  1) source your shell rc (bash: 'source ~/.bashrc'; zsh: 'source ~/.zshrc') — most installs put uv on PATH via ~/.local/bin"
            _warn "  2) install uv per https://docs.astral.sh/uv/ (curl -LsSf https://astral.sh/uv/install.sh | sh)"
            _warn "  3) invoke stage.sh from a session that already has uv on PATH (systemd user units, tmux server, etc.)"
            exit 78
        fi
        # DIVERGENCE AWARENESS (commission v0.16.6 c2 fix-back): when both
        # PATH-resolved `uv` AND $HOME/.local/bin/uv exist but resolve to
        # different binaries (different inodes / sizes / mtimes), surface
        # a non-blocking WARN. The PATH-resolved one wins (it was resolved
        # first), but the operator should know their session has two
        # installs. Skipped silently if only one resolves (the common case).
        if command -v uv >/dev/null 2>&1 && [ -x "$HOME/.local/bin/uv" ]; then
            _uv_path="$(command -v uv)"
            if [ "$_uv_path" != "$HOME/.local/bin/uv" ] \
                && ! cmp -s "$_uv_path" "$HOME/.local/bin/uv" 2>/dev/null; then
                _warn "uv DIVERGENCE: PATH=$_uv_path and \$HOME/.local/bin/uv resolve to DIFFERENT binaries (byte-different via cmp). Using PATH version; reconcile your install if this is unintentional."
            fi
        fi
        _log "PyInstaller build (bare, branch-safe — NEVER make build/pyinstaller: ensure-latest would yank the branch)"
        # _uv_bin is the absolute path resolved above — never rely on PATH
        # resolution at the subshell's exec time.
        if ! (cd "$REPO_ROOT" && rm -rf build/ && "$_uv_bin" run python -m PyInstaller ensemble.spec); then
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
# stage.sh:172-180 REPLACED — D-FA4.5 v0.15.3 supersession.
# GNU-debt sites OUT OF SCOPE (fence): lib.sh:84-89 _iso_to_epoch (BSD-only),
# lib.sh:706-712 cooldown arm (BSD date -v order), lib.sh:1238-1295 retention
# eviction (BSD stat -f). Fix family = uname dispatch; backlog, NOT this mission.
ROLLBACK_SAFE_SRC="unset"
ROLLBACK_SAFE="${ENSEMBLE_ROLLBACK_SAFE:-}"
case "$ROLLBACK_SAFE" in
    1|true)  ROLLBACK_SAFE=true ; ROLLBACK_SAFE_SRC="explicit=true" ;;
    0|false) ROLLBACK_SAFE=false; ROLLBACK_SAFE_SRC="explicit=false" ;;
    "")
        # unset: the full-history destructive-DDL grep (stage.sh:163-171) is
        # INFORMATIONAL — it is not delta-scoped and fires on DDL applied
        # before the previous release tag too. When it matches AND the
        # operator has not affirmed safety, refuse (D-FA4.5 supersession;
        # the silent-false path bit v0.14.2 on 2026-09-25 and v0.15.1 on
        # 2026-09-26). Override legitimacy: only set ENSEMBLE_ROLLBACK_SAFE=1
        # when the migration delta between the previous release tag and
        # $VERSION is EMPTY (no schema drift introduced by $VERSION itself).
        if [ "$CONTAINS_CONTRACT_PHASE" = "true" ]; then
            _warn "destructive DDL detected in daemon/migrations/versions (DROP TABLE|DROP COLUMN match) — refusing to derive rollback_safe silently (v0.15.3 D-FA4.5 supersession; the silent-false path bit v0.14.2 on 2026-09-25 and v0.15.1 on 2026-09-26). Re-run with an EXPLICIT choice:" \
                  "  ENSEMBLE_ROLLBACK_SAFE=1   ... ONLY if the migration delta between the previous release tag and $VERSION is EMPTY (no schema drift introduced by $VERSION — the destructive DDL was already applied before $VERSION)" \
                  "  ENSEMBLE_ROLLBACK_SAFE=0   ... otherwise (the delta is real and $VERSION genuinely cannot roll back)" \
                  "See ADR-035 in .agents/shared/planning/self-restart-upgrade-phase2/decisions.md and runbook docs/runbooks/upgrade-drills.md §2."
            exit 78
        else
            ROLLBACK_SAFE=true
            ROLLBACK_SAFE_SRC="default=true"
            _log "rollback_safe defaulted to true (no destructive DDL detected in migration history; no ENSEMBLE_ROLLBACK_SAFE override needed)"
        fi
        ;;
esac

# binary_version: the string the RUNNING daemon self-reports on /livez —
# daemon/api.py serves `from daemon import __version__` (daemon/__init__.py),
# and the manifest's binary_version is what gate_version compares that
# self-report against (D2). DERIVATION: the tag with the leading "v"
# stripped (tag v0.10.6 → "0.10.6") — i.e. the release flow RELIES on the
# tag matching daemon/__init__.py __version__; if they drift, the gate
# fails closed at promote (version-verify mismatch) even though the binary
# is fine. ENSEMBLE_BINARY_VERSION overrides for odd cases (stubs, drills).
BINARY_VERSION="${ENSEMBLE_BINARY_VERSION:-${VERSION#v}}"

# ── Lock (D5: stage mutates releases/ — serialize with promote/rollback) ────
if ! lock_acquire; then
    exit 78   # pipeline-busy already logged (structured, not an error)
fi
# Signal trap discipline (component 2 of r-20260928-005506-f82e): see
# lib.sh _trap_install_signal_handlers. Installs TERM/HUP/INT handlers
# that journal halt + release the lock; EXIT trap is swapped to the
# safe variant to avoid double-release. Stage holds this lock through
# LONG assembly phases (tree copies + sha256 walks can exceed
# LOCK_STALE_S=300s on big trees) — heartbeat at every phase boundary
# so a concurrent promote/sweep never stale-breaks the lock of a LIVE
# owner (review m3; promote/rollback heartbeat the same way)
_trap_install_signal_handlers "stage:lock"
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

# staged_at: REFRESHED on every stage (M6, commission v0.16.6 component 2).
# Pre-rider behavior preserved the original timestamp across idempotent
# re-stages, so the manifest was byte-identical — and tooling that
# sorts by `staged_at` (or surfaces "last re-stage was N days ago")
# could not tell the operator's restage event apart from a no-op.
# The motivating bug: 16:22:32Z survived a 17:50 re-stage, masking the
# restage event. Write the FRESH now-iso unconditionally (idempotency
# is preserved by the per-file checksums, which DO stay stable across
# identical re-stages — operators wanting identity compare can diff
# manifest.json checksums before/after; those ARE stable).
#
# EVICTION IDENTITY (lib.sh:2839-2853, retention): the key the retention
# sort uses is the `staged_at` EPOCH (manifest-stamped ISO converted via
# _iso_to_epoch; dir mtime fallback when the manifest is missing). NOT
# the checksums — checksums are wobble-prevention for IDENTITY (re-stage
# of the same payload stays at the same retention position because
# staged_at moves with the restage event, not because checksums are
# stable). The TWO roles do NOT conflict: checksums gate IDENTITY (you
# can trust the binary); staged_at EPOCH sorts the EVICTION list (oldest
# staged_at = first to evict).
STAGED_AT="$(_now_iso)"

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

# swap in — RENAME-ASIDE, not rm-then-mv (Batch C, council 🟢 suggestion
# taken): the old `rm -rf "$REL"; mv …` left a window with NEITHER the old
# nor the new release dir — a crash there destroyed the rollback target.
# Rename-aside keeps the old payload on disk until the new one is in place;
# a crash mid-swap leaves either the aside dir (recoverable/evictable) or
# both, never neither. No flip — `current` is untouched throughout.
if [ -e "$REL" ]; then
    REL_ASIDE="$REL.aside.$$"
    if ! mv "$REL" "$REL_ASIDE"; then
        _warn "failed to move the existing $REL aside — keeping it in place"
        rm -rf "$STAGE_TMP"
        exit 1
    fi
    if mv "$STAGE_TMP" "$REL"; then
        rm -rf "$REL_ASIDE"
    else
        _warn "failed to move staged release into place — restoring the previous payload"
        mv "$REL_ASIDE" "$REL" || _warn "RESTORE FAILED: $REL_ASIDE must be moved back to $REL by hand"
        rm -rf "$STAGE_TMP"
        exit 1
    fi
else
    if ! mv "$STAGE_TMP" "$REL"; then
        _warn "failed to move staged release into place"
        rm -rf "$STAGE_TMP"
        exit 1
    fi
fi

# ── Journal init (staged mode begins) ───────────────────────────────────────
journal_init
# a re-staged version is a REBUILT artifact — a prior quarantine verdict no
# longer describes it (the operator explicitly rebuilt + re-verified it)
journal_quarantine_clear "$VERSION"

# ── ENSEMBLE_SELF_ENV marker → INSTALL_DIR/.env (D-FA2.3; HIGHEST-PRIORITY) ─
#
# 2026-09-22 supersession: the marker is OPTIONAL (operator opt-out via
# ``ENSEMBLE_SELF_ENV=0|false|no|off`` preserves today's fail-closed
# contract verbatim). Auto-derivation (frozen-binary + releases/ for
# sandbox; install-dir + POSTGRES_DB cross-check for live/demo; dev-
# shape POSTGRES_DB for dev) covers installs that never had a marker
# (e.g. the LIVE daemon at port 9797 which pre-dates P2.1). Stage.sh
# still stages the marker so an explicit staging intent overrides any
# ambient host evidence AND so older daemons pick it up on the next
# stage. The marker is consumed first by ``_self_env_marker`` (P2.2
# tool layer).
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

_log "staged release $VERSION at $REL (rollback_safe=$ROLLBACK_SAFE source=$ROLLBACK_SAFE_SRC known_schema_gen=$KNOWN_SCHEMA_GEN contains_contract_phase=$CONTAINS_CONTRACT_PHASE) — NO flip performed (promote.sh owns current)"
exit 0
